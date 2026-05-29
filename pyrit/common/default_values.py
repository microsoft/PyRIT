# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_required_value(*, env_var_name: str, passed_value: Any) -> Any:
    """
    Get a required value from an environment variable or a passed value,
    preferring the passed value.

    If no value is found, raises a KeyError

    Args:
        env_var_name (str): The name of the environment variable to check
        passed_value: The value passed to the function. Can be a string or a callable that returns a string.

    Returns:
        The passed value if provided (preserving type for callables), otherwise the value from the environment variable.

    Raises:
        ValueError: If neither the passed value nor the environment variable is provided.
    """
    if passed_value:
        # Preserve callables (e.g., token providers for Entra auth)
        if callable(passed_value):
            return passed_value
        return str(passed_value)

    value = os.environ.get(env_var_name)
    if value:
        return value

    raise ValueError(f"Environment variable {env_var_name} is required")


def get_non_required_value(*, env_var_name: str, passed_value: Optional[str] = None) -> str:
    """
    Get a non-required value from an environment variable or a passed value,
    preferring the passed value.

    Args:
        env_var_name (str): The name of the environment variable to check.
        passed_value (str): The value passed to the function.

    Returns:
        str: The passed value if provided, otherwise the value from the environment variable.
             If no value is found, returns an empty string.
    """
    if passed_value:
        return passed_value

    value = os.environ.get(env_var_name)
    if value:
        return value

    return ""
