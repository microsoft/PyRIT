# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Core scenario classes for running attack configurations."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models.parameter import Parameter
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.attack_technique import AttackTechnique
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory, ScorerOverridePolicy
    from pyrit.scenario.core.dataset_configuration import (
        INLINE_DATASET_NAME,
        CompoundDatasetAttackConfiguration,
        DatasetAttackConfiguration,
        DatasetConfiguration,
        DatasetConstraintError,
        DatasetSourceKind,
        ResolvedDataset,
        require_nonempty,
    )
    from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
    from pyrit.scenario.core.scenario_target_defaults import get_default_adversarial_target, get_default_scorer_target
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AtomicAttack": "pyrit.scenario.core.atomic_attack",
    "AttackTechnique": "pyrit.scenario.core.attack_technique",
    "AttackTechniqueFactory": "pyrit.scenario.core.attack_technique_factory",
    "BaselineAttackPolicy": "pyrit.scenario.core.scenario",
    "CompoundDatasetAttackConfiguration": "pyrit.scenario.core.dataset_configuration",
    "DatasetAttackConfiguration": "pyrit.scenario.core.dataset_configuration",
    "DatasetConfiguration": "pyrit.scenario.core.dataset_configuration",
    "DatasetConstraintError": "pyrit.scenario.core.dataset_configuration",
    "DatasetSourceKind": "pyrit.scenario.core.dataset_configuration",
    "INLINE_DATASET_NAME": "pyrit.scenario.core.dataset_configuration",
    "Parameter": "pyrit.models.parameter",
    "ResolvedDataset": "pyrit.scenario.core.dataset_configuration",
    "require_nonempty": "pyrit.scenario.core.dataset_configuration",
    "Scenario": "pyrit.scenario.core.scenario",
    "ScenarioTechnique": "pyrit.scenario.core.scenario_technique",
    "ScorerOverridePolicy": "pyrit.scenario.core.attack_technique_factory",
    "get_default_scorer_target": "pyrit.scenario.core.scenario_target_defaults",
    "get_default_adversarial_target": "pyrit.scenario.core.scenario_target_defaults",
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
