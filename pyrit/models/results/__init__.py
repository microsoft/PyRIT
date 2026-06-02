# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Results module - strategy and attack result types for PyRIT.

- StrategyResult: Base class for all strategy results.
- AttackResult: Result of an attack execution, with conversation/scoring evidence.
- AttackOutcome: Enum of possible attack outcomes.
"""

from pyrit.models.results.attack_result import AttackOutcome, AttackResult, AttackResultT
from pyrit.models.results.strategy_result import StrategyResult, StrategyResultT

__all__ = [
    "AttackOutcome",
    "AttackResult",
    "AttackResultT",
    "StrategyResult",
    "StrategyResultT",
]
