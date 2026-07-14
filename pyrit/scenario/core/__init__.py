# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Core scenario classes for running attack configurations."""

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

__all__ = [
    "AtomicAttack",
    "AttackTechnique",
    "AttackTechniqueFactory",
    "BaselineAttackPolicy",
    "CompoundDatasetAttackConfiguration",
    "DatasetAttackConfiguration",
    "DatasetConfiguration",
    "DatasetConstraintError",
    "DatasetSourceKind",
    "INLINE_DATASET_NAME",
    "Parameter",
    "ResolvedDataset",
    "require_nonempty",
    "Scenario",
    "ScenarioTechnique",
    "ScorerOverridePolicy",
    "get_default_scorer_target",
    "get_default_adversarial_target",
]
