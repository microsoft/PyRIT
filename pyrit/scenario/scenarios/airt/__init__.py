# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AIRT scenario classes."""

from typing import Any

from pyrit.scenario.scenarios.airt.content_harms import ContentHarms
from pyrit.scenario.scenarios.airt.cyber import Cyber
from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak, JailbreakStrategy
from pyrit.scenario.scenarios.airt.leakage import Leakage, LeakageStrategy
from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial, PsychosocialStrategy
from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse
from pyrit.scenario.scenarios.airt.scam import Scam, ScamStrategy


def __getattr__(name: str) -> Any:
    """
    Lazily resolve dynamic strategy classes.

    Returns:
        Any: The resolved strategy class.

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "RapidResponseStrategy":
        return RapidResponse.get_strategy_class()
    if name == "ContentHarmsStrategy":
        return ContentHarms.get_strategy_class()
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
