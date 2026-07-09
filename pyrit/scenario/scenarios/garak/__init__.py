# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Garak-based attack scenarios."""

from typing import Any

from pyrit.scenario.scenarios.garak.doctor import Doctor, _build_doctor_strategy
from pyrit.scenario.scenarios.garak.encoding import Encoding, EncodingStrategy
from pyrit.scenario.scenarios.garak.web_injection import WebInjection, WebInjectionStrategy


def __getattr__(name: str) -> Any:
    """
    Lazily resolve the dynamically-generated Doctor strategy class.

    Returns:
        Any: The resolved strategy class.

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "DoctorStrategy":
        return _build_doctor_strategy()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Doctor",
    "DoctorStrategy",
    "Encoding",
    "EncodingStrategy",
    "WebInjection",
    "WebInjectionStrategy",
]
