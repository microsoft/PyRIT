# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated — use ``rapid_response`` instead.

``ContentHarms`` and ``ContentHarmsStrategy`` are thin aliases kept for
backward compatibility. They will be removed in v0.15.0.
"""

from typing import TYPE_CHECKING, Any

from pyrit.common.deprecation import print_deprecation_message

if TYPE_CHECKING:
    from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse as ContentHarms  # noqa: F401

    ContentHarmsStrategy = Any


def __getattr__(name: str) -> Any:
    """
    Lazily resolve deprecated aliases and emit a deprecation warning.

    Returns:
        Any: The resolved alias (``RapidResponse`` or its strategy class).

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "ContentHarms":
        print_deprecation_message(
            old_item="pyrit.scenario.scenarios.airt.content_harms.ContentHarms",
            new_item="pyrit.scenario.scenarios.airt.rapid_response.RapidResponse",
            removed_in="0.15.0",
        )
        from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse

        return RapidResponse
    if name == "ContentHarmsStrategy":
        print_deprecation_message(
            old_item="pyrit.scenario.scenarios.airt.content_harms.ContentHarmsStrategy",
            new_item="pyrit.scenario.scenarios.airt.rapid_response.RapidResponse.get_strategy_class()",
            removed_in="0.15.0",
        )
        from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse

        return RapidResponse.get_strategy_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ContentHarms", "ContentHarmsStrategy"]
