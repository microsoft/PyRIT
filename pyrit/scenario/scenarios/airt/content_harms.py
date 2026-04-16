# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated — use ``rapid_response`` instead.

``ContentHarms`` and ``ContentHarmsStrategy`` are thin aliases kept for
backward compatibility.  They will be removed in a future release.
"""

import warnings

from pyrit.scenario.scenarios.airt.rapid_response import (
    RapidResponse,
    RapidResponseStrategy,
)


def __getattr__(name: str):
    if name == "ContentHarms":
        warnings.warn(
            "ContentHarms is deprecated. Use RapidResponse instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return RapidResponse
    if name == "ContentHarmsStrategy":
        warnings.warn(
            "ContentHarmsStrategy is deprecated. Use RapidResponseStrategy instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return RapidResponseStrategy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Direct aliases for import-from statements
ContentHarms = RapidResponse
ContentHarmsStrategy = RapidResponseStrategy
