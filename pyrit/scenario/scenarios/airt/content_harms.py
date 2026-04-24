# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated — use ``rapid_response`` instead.

``ContentHarms`` and ``ContentHarmsStrategy`` are thin aliases kept for
backward compatibility.  They will be removed in v0.15.0.
"""

from typing import Any

from pyrit.scenario.scenarios.airt.rapid_response import (
    RapidResponse as ContentHarms,
)


def __getattr__(name: str) -> Any:
    """
    Lazily resolve deprecated strategy class.

    Returns:
        Any: The resolved strategy class.

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "ContentHarmsStrategy":
        return ContentHarms.get_strategy_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ContentHarms", "ContentHarmsStrategy"]
