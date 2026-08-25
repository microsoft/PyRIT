# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Results module - strategy, attack, and scenario result types for PyRIT.

- StrategyResult: Base class for all strategy results.
- AttackResult: Result of an attack execution, with conversation/scoring evidence.
- AttackOutcome: Enum of possible attack outcomes.
- ScenarioResult: Aggregate result of a scenario run.
- ScenarioIdentifier: Identifier describing the executed scenario.
- ScenarioRunState: Lifecycle state of a scenario run.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models.identifiers.scenario_identifier import ScenarioIdentifier
    from pyrit.models.results.attack_result import AttackOutcome, AttackResult, AttackResultT
    from pyrit.models.results.scenario_result import ScenarioResult, ScenarioRunState
    from pyrit.models.results.strategy_result import StrategyResult, StrategyResultT

_LAZY_EXPORTS: dict[str, str] = {
    "AttackOutcome": "pyrit.models.results.attack_result",
    "AttackResult": "pyrit.models.results.attack_result",
    "AttackResultT": "pyrit.models.results.attack_result",
    "ScenarioIdentifier": "pyrit.models.identifiers.scenario_identifier",
    "ScenarioResult": "pyrit.models.results.scenario_result",
    "ScenarioRunState": "pyrit.models.results.scenario_result",
    "StrategyResult": "pyrit.models.results.strategy_result",
    "StrategyResultT": "pyrit.models.results.strategy_result",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    """
    Resolve a public result export on first access.

    Args:
        name (str): The requested public name.

    Returns:
        object: The resolved export.
    """
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    """Return package attributes, including unresolved exports."""
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
