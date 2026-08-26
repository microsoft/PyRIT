# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Garak-based attack scenarios."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.scenario.scenarios._dynamic_techniques import DoctorTechnique
    from pyrit.scenario.scenarios.garak.audio_achilles_heel import AudioAchillesHeel, AudioAchillesHeelTechnique
    from pyrit.scenario.scenarios.garak.doctor import Doctor
    from pyrit.scenario.scenarios.garak.encoding import Encoding, EncodingTechnique
    from pyrit.scenario.scenarios.garak.figstep import FigStep, FigStepTechnique
    from pyrit.scenario.scenarios.garak.package_hallucination import (
        PackageHallucination,
        PackageHallucinationTechnique,
    )
    from pyrit.scenario.scenarios.garak.system_prompt_extraction import (
        SystemPromptExtraction,
        SystemPromptExtractionTechnique,
    )
    from pyrit.scenario.scenarios.garak.web_injection import WebInjection, WebInjectionTechnique

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AudioAchillesHeel": "pyrit.scenario.scenarios.garak.audio_achilles_heel",
    "AudioAchillesHeelTechnique": "pyrit.scenario.scenarios.garak.audio_achilles_heel",
    "Doctor": "pyrit.scenario.scenarios.garak.doctor",
    "DoctorTechnique": "pyrit.scenario.scenarios._dynamic_techniques",
    "Encoding": "pyrit.scenario.scenarios.garak.encoding",
    "EncodingTechnique": "pyrit.scenario.scenarios.garak.encoding",
    "FigStep": "pyrit.scenario.scenarios.garak.figstep",
    "FigStepTechnique": "pyrit.scenario.scenarios.garak.figstep",
    "PackageHallucination": "pyrit.scenario.scenarios.garak.package_hallucination",
    "PackageHallucinationTechnique": "pyrit.scenario.scenarios.garak.package_hallucination",
    "SystemPromptExtraction": "pyrit.scenario.scenarios.garak.system_prompt_extraction",
    "SystemPromptExtractionTechnique": "pyrit.scenario.scenarios.garak.system_prompt_extraction",
    "WebInjection": "pyrit.scenario.scenarios.garak.web_injection",
    "WebInjectionTechnique": "pyrit.scenario.scenarios.garak.web_injection",
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
