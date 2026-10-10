# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""AIRT scenario classes."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.scenario.scenarios._dynamic_techniques import (
        CyberTechnique,
        JailbreakTechnique,
        LeakageTechnique,
        MultilingualTechnique,
        RapidResponseTechnique,
    )
    from pyrit.scenario.scenarios.airt.cyber import Cyber
    from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak
    from pyrit.scenario.scenarios.airt.leakage import Leakage
    from pyrit.scenario.scenarios.airt.multilingual import Multilingual
    from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial, PsychosocialTechnique
    from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse
    from pyrit.scenario.scenarios.airt.scam import Scam, ScamTechnique

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "Cyber": "pyrit.scenario.scenarios.airt.cyber",
    "CyberTechnique": "pyrit.scenario.scenarios._dynamic_techniques",
    "Jailbreak": "pyrit.scenario.scenarios.airt.jailbreak",
    "JailbreakTechnique": "pyrit.scenario.scenarios._dynamic_techniques",
    "Leakage": "pyrit.scenario.scenarios.airt.leakage",
    "LeakageTechnique": "pyrit.scenario.scenarios._dynamic_techniques",
    "Multilingual": "pyrit.scenario.scenarios.airt.multilingual",
    "MultilingualTechnique": "pyrit.scenario.scenarios._dynamic_techniques",
    "Psychosocial": "pyrit.scenario.scenarios.airt.psychosocial",
    "PsychosocialTechnique": "pyrit.scenario.scenarios.airt.psychosocial",
    "RapidResponse": "pyrit.scenario.scenarios.airt.rapid_response",
    "RapidResponseTechnique": "pyrit.scenario.scenarios._dynamic_techniques",
    "Scam": "pyrit.scenario.scenarios.airt.scam",
    "ScamTechnique": "pyrit.scenario.scenarios.airt.scam",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
