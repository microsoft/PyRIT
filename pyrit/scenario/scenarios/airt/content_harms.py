# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated — use ``rapid_response`` instead.

``ContentHarms`` and ``ContentHarmsStrategy`` are thin aliases kept for
backward compatibility.  They will be removed in a future release.
"""

from pyrit.scenario.scenarios.airt.rapid_response import (
    RapidResponse as ContentHarms,
)


def __getattr__(name: str):
    if name == "ContentHarmsStrategy":
        return ContentHarms.get_strategy_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ContentHarms", "ContentHarmsStrategy"]
