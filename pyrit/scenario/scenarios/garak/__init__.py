# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Garak-based attack scenarios."""

from pyrit.scenario.scenarios.garak.encoding import Encoding, EncodingStrategy
from pyrit.scenario.scenarios.garak.web_injection import (
    WebInjection,
    WebInjectionStrategy,
)

__all__ = [
    "Encoding",
    "EncodingStrategy",
    "WebInjection",
    "WebInjectionStrategy",
]
