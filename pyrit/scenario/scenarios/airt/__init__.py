# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AIRT scenario classes."""

import importlib
from typing import TYPE_CHECKING, Any

from pyrit.scenario.scenarios.airt.cyber import Cyber
from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak, JailbreakStrategy
from pyrit.scenario.scenarios.airt.leakage import Leakage
from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial, PsychosocialStrategy
from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse
from pyrit.scenario.scenarios.airt.scam import Scam, ScamStrategy

if TYPE_CHECKING:
    from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse as ContentHarms  # noqa: F401

    ContentHarmsStrategy = Any


def __getattr__(name: str) -> Any:
    """
    Lazily resolve dynamic strategy classes and deprecated aliases.

    Returns:
        Any: The resolved strategy class or deprecated alias.

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "RapidResponseStrategy":
        return RapidResponse.get_strategy_class()
    if name == "LeakageStrategy":
        return Leakage.get_strategy_class()
    if name in ("ContentHarms", "ContentHarmsStrategy"):
        # Delegate to the content_harms module so it can emit the deprecation
        # warning. We import lazily here to avoid triggering the warning on
        # every `import pyrit.scenario.scenarios.airt`.
        content_harms = importlib.import_module("pyrit.scenario.scenarios.airt.content_harms")
        return getattr(content_harms, name)
    if name == "CyberStrategy":
        return Cyber.get_strategy_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ContentHarms",
    "ContentHarmsStrategy",
    "Cyber",
    "CyberStrategy",
    "Jailbreak",
    "JailbreakStrategy",
    "Leakage",
    "LeakageStrategy",
    "Psychosocial",
    "PsychosocialStrategy",
    "RapidResponse",
    "RapidResponseStrategy",
    "Scam",
    "ScamStrategy",
]
