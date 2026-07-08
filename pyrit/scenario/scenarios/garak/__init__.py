# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Garak-based attack scenarios."""

from pyrit.scenario.scenarios.garak.audio_achilles_heel import AudioAchillesHeel, AudioAchillesHeelStrategy
from pyrit.scenario.scenarios.garak.doctor import Doctor, DoctorStrategy
from pyrit.scenario.scenarios.garak.encoding import Encoding, EncodingStrategy
from pyrit.scenario.scenarios.garak.web_injection import WebInjection, WebInjectionStrategy

__all__ = [
    "AudioAchillesHeel",
    "AudioAchillesHeelStrategy",
    "Doctor",
    "DoctorStrategy",
    "Encoding",
    "EncodingStrategy",
    "WebInjection",
    "WebInjectionStrategy",
]
