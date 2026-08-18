# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Garak-based attack scenarios."""

from typing import Any

from pyrit.scenario.scenarios.garak.audio_achilles_heel import AudioAchillesHeel, AudioAchillesHeelTechnique
from pyrit.scenario.scenarios.garak.doctor import Doctor, _build_doctor_technique
from pyrit.scenario.scenarios.garak.encoding import Encoding, EncodingTechnique
from pyrit.scenario.scenarios.garak.package_hallucination import (
    PackageHallucination,
    PackageHallucinationTechnique,
)
from pyrit.scenario.scenarios.garak.system_prompt_extraction import (
    SystemPromptExtraction,
    SystemPromptExtractionTechnique,
)
from pyrit.scenario.scenarios.garak.web_injection import WebInjection, WebInjectionTechnique


def __getattr__(name: str) -> Any:
    """
    Lazily resolve the dynamically-generated Doctor technique class.

    Returns:
        Any: The resolved technique class.

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "DoctorTechnique":
        return _build_doctor_technique()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AudioAchillesHeel",
    "AudioAchillesHeelTechnique",
    "Doctor",
    "DoctorTechnique",
    "Encoding",
    "EncodingTechnique",
    "PackageHallucination",
    "PackageHallucinationTechnique",
    "SystemPromptExtraction",
    "SystemPromptExtractionTechnique",
    "WebInjection",
    "WebInjectionTechnique",
]
