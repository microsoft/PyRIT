# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AIRT scenario classes."""

from typing import Any

from pyrit.scenario.scenarios.airt.cyber import Cyber, _build_cyber_strategy
from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak, JailbreakStrategy
from pyrit.scenario.scenarios.airt.leakage import Leakage, _build_leakage_strategy
from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial, PsychosocialStrategy
from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse, _build_rapid_response_strategy
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
        return _build_rapid_response_strategy()
    if name == "LeakageStrategy":
        return _build_leakage_strategy()
    if name == "CyberStrategy":
        return _build_cyber_strategy()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
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
