# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Core attack strategy module."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_config import (
        AttackAdversarialConfig,
        AttackConverterConfig,
        AttackScoringConfig,
        resolve_adversarial_json_schema,
        resolve_adversarial_system_prompt,
    )
    from pyrit.executor.attack.core.attack_executor import AttackExecutor, AttackExecutorResult
    from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
    from pyrit.executor.attack.core.attack_strategy import (
        AttackContext,
        AttackStrategy,
        AttackStrategyContextT,
        AttackStrategyResultT,
        attack_outcome_from_score,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AttackParameters": "pyrit.executor.attack.core.attack_parameters",
    "AttackParamsT": "pyrit.executor.attack.core.attack_parameters",
    "AttackStrategy": "pyrit.executor.attack.core.attack_strategy",
    "AttackContext": "pyrit.executor.attack.core.attack_strategy",
    "AttackConverterConfig": "pyrit.executor.attack.core.attack_config",
    "AttackScoringConfig": "pyrit.executor.attack.core.attack_config",
    "AttackAdversarialConfig": "pyrit.executor.attack.core.attack_config",
    "AttackStrategyContextT": "pyrit.executor.attack.core.attack_strategy",
    "AttackStrategyResultT": "pyrit.executor.attack.core.attack_strategy",
    "AttackExecutor": "pyrit.executor.attack.core.attack_executor",
    "AttackExecutorResult": "pyrit.executor.attack.core.attack_executor",
    "attack_outcome_from_score": "pyrit.executor.attack.core.attack_strategy",
    "resolve_adversarial_json_schema": "pyrit.executor.attack.core.attack_config",
    "resolve_adversarial_system_prompt": "pyrit.executor.attack.core.attack_config",
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
