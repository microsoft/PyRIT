# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private recipe contract and constructor validation for code-owned scenario suites."""

import copy
from typing import Any, ClassVar, Protocol

from pyrit.models import ScenarioPreset


class _SuiteRecipe(Protocol):
    """Structural contract for recipes; implementations need not inherit from this type."""

    DESCRIPTION: ClassVar[str]

    def build_presets(self, *, suite_name: str) -> list[ScenarioPreset]:
        """
        Build the suite's presets in launch order.

        Args:
            suite_name (str): Registry name to use as the preset-name prefix.

        Returns:
            list[ScenarioPreset]: The configured presets.
        """
        ...


def _resolve_suite_arguments(*, suite_class: type[_SuiteRecipe], args: dict[str, Any]) -> dict[str, Any]:
    """
    Validate native configuration values against a builder's constructor signature.

    Args:
        suite_class (type[_SuiteRecipe]): The recipe to configure.
        args (dict[str, Any]): Merged catalog arguments and operator overrides.

    Returns:
        dict[str, Any]: Independent validated values, including constructor defaults.

    Raises:
        ValueError: If names, types, or required arguments are invalid.
    """
    from pyrit.registry.resolution import derive_parameters

    parameters = derive_parameters(cls=suite_class)
    unknown = args.keys() - {parameter.name for parameter in parameters}
    if unknown:
        raise ValueError(f"Unknown arguments for {suite_class.__name__}: {', '.join(sorted(unknown))}.")
    resolved: dict[str, Any] = {}
    for parameter in parameters:
        value = args.get(parameter.name)
        if value is None:
            if parameter.required:
                raise ValueError(f"{suite_class.__name__} requires '{parameter.name}'.")
            value = parameter.default
        if value is not None:
            try:
                value = parameter.validate_json_value(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Parameter '{parameter.name}' expects {parameter.type_name}: {exc}") from exc
        resolved[parameter.name] = copy.deepcopy(value)
    return resolved
