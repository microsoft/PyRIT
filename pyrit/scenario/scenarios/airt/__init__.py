# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AIRT scenario classes."""

from typing import Any

from pyrit.scenario.scenarios.airt.cyber import Cyber, _build_cyber_technique
from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak, _build_jailbreak_technique
from pyrit.scenario.scenarios.airt.leakage import Leakage, _build_leakage_technique
from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial, PsychosocialTechnique
from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse, _build_rapid_response_technique
from pyrit.scenario.scenarios.airt.scam import Scam, ScamTechnique


def __getattr__(name: str) -> Any:
    """
    Lazily resolve dynamic technique classes.

    Returns:
        Any: The resolved technique class.

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "RapidResponseTechnique":
        return _build_rapid_response_technique()
    if name == "LeakageTechnique":
        return _build_leakage_technique()
    if name == "CyberTechnique":
        return _build_cyber_technique()
    if name == "JailbreakTechnique":
        return _build_jailbreak_technique()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Cyber",
    "CyberTechnique",
    "Jailbreak",
    "JailbreakTechnique",
    "Leakage",
    "LeakageTechnique",
    "Psychosocial",
    "PsychosocialTechnique",
    "RapidResponse",
    "RapidResponseTechnique",
    "Scam",
    "ScamTechnique",
]
