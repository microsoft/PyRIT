# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import logging
import math
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

logger = logging.getLogger(__name__)


def verify_and_resolve_path(path: Union[str, Path]) -> Path:
    """
    Verify that a path is valid and resolve it to an absolute path.

    This utility function can be used anywhere path validation is needed,
    such as in scorers, converters, or other components that accept file paths.

    Args:
        path (Union[str, Path]): A path as a string or Path object.

    Returns:
        Path: The resolved absolute Path object.

    Raises:
        ValueError: If the path is not a string or Path object.
        FileNotFoundError: If the path does not exist.
    """
    if not isinstance(path, (str, Path)):
        raise ValueError(f"Path must be a string or Path object. Got type: {type(path).__name__}")

    path_obj: Path = Path(path).resolve() if isinstance(path, str) else path.resolve()
    if not path_obj.exists():
        raise FileNotFoundError(f"Path not found: {str(path_obj)}")
    return path_obj


def combine_dict(
    existing_dict: Optional[dict[str, Any]] = None, new_dict: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """
    Combine two dictionaries containing string keys and values into one.

    Args:
        existing_dict: Dictionary with existing values
        new_dict: Dictionary with new values to be added to the existing dictionary.
            Note if there's a key clash, the value in new_dict will be used.

    Returns:
        dict: combined dictionary
    """
    result = {**(existing_dict or {})}
    result.update(new_dict or {})
    return result


def combine_list(list1: Union[str, List[str]], list2: Union[str, List[str]]) -> list[str]:
    """
    Combine two lists or strings into a single list with unique values.

    Args:
        list1 (Union[str, List[str]]): First list or string to combine.
        list2 (Union[str, List[str]]): Second list or string to combine.

    Returns:
        list: Combined list containing unique values from both inputs.
    """
    if isinstance(list1, str):
        list1 = [list1]
    if isinstance(list2, str):
        list2 = [list2]

    # Merge and keep only unique values
    combined = list(set(list1 + list2))
    return combined


def get_random_indices(*, start: int, size: int, proportion: float) -> List[int]:
    """
    Generate a list of random indices based on the specified proportion of a given size.
    The indices are selected from the range [start, start + size).

    Args:
        start (int): Starting index (inclusive). It's the first index that could possibly be selected.
        size (int): Size of the collection to select from. This is the total number of indices available.
            For example, if `start` is 0 and `size` is 10, the available indices are [0, 1, 2, ..., 9].
        proportion (float): The proportion of indices to select from the total size. Must be between 0 and 1.
            For example, if `proportion` is 0.5 and `size` is 10, 5 randomly selected indices will be returned.

    Returns:
        List[int]: A list of randomly selected indices based on the specified proportion.

    Raises:
        ValueError: If `start` is negative, `size` is not positive, or `proportion` is not between 0 and 1.
    """
    if start < 0:
        raise ValueError("Start index must be non-negative")
    if size <= 0:
        raise ValueError("Size must be greater than 0")
    if proportion < 0 or proportion > 1:
        raise ValueError("Proportion must be between 0 and 1")

    if proportion == 0:
        return []
    if proportion == 1:
        return list(range(start, start + size))

    n = max(math.ceil(size * proportion), 1)  # the number of indices to select
    return random.sample(range(start, start + size), n)


def to_sha256(data: str) -> str:
    """
    Convert a string to its SHA-256 hash representation.

    Args:
        data (str): The input string to be hashed.

    Returns:
        str: The SHA-256 hash of the input string, represented as a hexadecimal string.
    """
    return hashlib.sha256(data.encode()).hexdigest()


def warn_if_set(
    *, config: Any, unused_fields: List[str], log: Union[logging.Logger, logging.LoggerAdapter[logging.Logger]] = logger
) -> None:
    """
    Warn about unused parameters in configurations.

    This method checks if specified fields in a configuration object are set
    (not None and not empty for collections) and logs a warning message for each
    field that will be ignored by the current attack strategy.

    Args:
        config (Any): The configuration object to check for unused fields.
        unused_fields (List[str]): List of field names to check in the config object.
        log (Union[logging.Logger, logging.LoggerAdapter]): Logger to use for warning messages.
    """
    config_name = config.__class__.__name__

    for field_name in unused_fields:
        # Get the field value from the config object
        if not hasattr(config, field_name):
            log.warning(f"Field '{field_name}' does not exist in {config_name}. Skipping unused parameter check.")
            continue

        param_value = getattr(config, field_name)

        # Check if the parameter is set
        is_set = False
        if param_value is not None:
            # For collections, also check if they are not empty
            if hasattr(param_value, "__len__"):
                is_set = len(param_value) > 0
            else:
                is_set = True

        if is_set:
            log.warning(f"{field_name} was provided in {config_name} but is not used. This parameter will be ignored.")


_T = TypeVar("_T")


def get_kwarg_param(
    *,
    kwargs: Dict[str, Any],
    param_name: str,
    expected_type: Type[_T],
    required: bool = True,
    default_value: Optional[_T] = None,
) -> Optional[_T]:
    """
    Validate and extract a parameter from kwargs.

    Args:
        kwargs (Dict[str, Any]): The dictionary containing parameters.
        param_name (str): The name of the parameter to validate.
        expected_type (Type[_T]): The expected type of the parameter.
        required (bool): Whether the parameter is required. If True, raises ValueError if missing.
        default_value (Optional[_T]): Default value to return if the parameter is not required and not present.

    Returns:
        Optional[_T]: The validated parameter value if present and valid, otherwise None.

    Args:
        kwargs (Dict[str, Any]): The dictionary containing parameters.
        param_name (str): The name of the parameter to validate.
        expected_type (Type[_T]): The expected type of the parameter.
        required (bool): Whether the parameter is required. If True, raises ValueError if missing.
        default_value (Optional[_T]): Default value to return if the parameter is not required and not present.

    Returns:
        Optional[_T]: The validated parameter value if present and valid, otherwise None.

    Raises:
        ValueError: If the parameter is missing or None.
        TypeError: If the parameter is not of the expected type.
    """
    if param_name not in kwargs:
        if not required:
            return default_value
        raise ValueError(f"Missing required parameter: {param_name}")

    value = kwargs.pop(param_name)

    if not value:
        if not required:
            return default_value
        raise ValueError(f"Parameter '{param_name}' must be provided and non-empty")

    if not isinstance(value, expected_type):
        raise TypeError(
            f"Parameter '{param_name}' must be of type {expected_type.__name__}, got {type(value).__name__}"
        )

    return value
