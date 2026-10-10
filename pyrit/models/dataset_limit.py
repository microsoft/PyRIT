# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared input values for scenario dataset limits."""

from typing import Literal

ResolvedDatasetLimit = int | Literal["all"]
DatasetLimit = ResolvedDatasetLimit | Literal["default", ""] | None


def normalize_dataset_limit(value: object) -> ResolvedDatasetLimit | Literal["default"]:
    """
    Normalize a dataset limit without choosing a scenario-specific default.

    Returns:
        ResolvedDatasetLimit | Literal["default"]: A positive cap, all, or default.

    Raises:
        ValueError: If the value is not a supported dataset limit.
    """
    if value is None:
        return "default"
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ("", "default"):
            return "default"
        if value == "all":
            return "all"
        try:
            value = int(value)
        except ValueError:
            raise ValueError("Dataset limit must be a positive integer, 'default', or 'all'.") from None
    if type(value) is not int or value < 1:
        raise ValueError("Dataset limit must be a positive integer, 'default', or 'all'.")
    return value
