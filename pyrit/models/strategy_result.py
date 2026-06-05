# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Backward-compatibility shim.

``StrategyResult`` now lives in ``pyrit.models.results``. Import from there (or
from ``pyrit.models``) instead. This module re-exports the public names so
existing ``from pyrit.models.strategy_result import ...`` imports keep working.
"""

from typing import Any

from pyrit.models.results import strategy_result as _strategy_result
from pyrit.models.results.strategy_result import StrategyResult, StrategyResultT


def __getattr__(name: str) -> Any:
    return getattr(_strategy_result, name)


__all__ = ["StrategyResult", "StrategyResultT"]
