# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Results module - strategy, attack, and scenario result types for PyRIT.

- StrategyResult: Base class for all strategy results.
- AttackResult: Result of an attack execution, with conversation/scoring evidence.
- AttackOutcome: Enum of possible attack outcomes.
- ScenarioResult: Aggregate result of a scenario run.
- ScenarioIdentifier: Identifier describing the executed scenario.
- ScenarioRunState: Lifecycle state of a scenario run.
"""

from pyrit.models.identifiers.scenario_identifier import ScenarioIdentifier
from pyrit.models.results.attack_result import AttackOutcome, AttackResult, AttackResultT
from pyrit.models.results.scenario_result import (
    ScenarioResult,
    ScenarioRunState,
)
from pyrit.models.results.strategy_result import StrategyResult, StrategyResultT

__all__ = [
    "AttackOutcome",
    "AttackResult",
    "AttackResultT",
    "ScenarioIdentifier",
    "ScenarioResult",
    "ScenarioRunState",
    "StrategyResult",
    "StrategyResultT",
]
