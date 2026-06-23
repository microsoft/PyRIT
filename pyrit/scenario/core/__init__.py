# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Core scenario classes for running attack configurations."""

from pyrit.common.parameter import Parameter
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory, ScorerOverridePolicy
from pyrit.scenario.core.dataset_configuration import (
    INLINE_DATASET_NAME,
    DatasetAttackConfiguration,
    DatasetConfiguration,
    DatasetConstraintError,
    DatasetObjectiveConfiguration,
    DatasetPromptConfiguration,
    DatasetSourceKind,
    ResolvedDataset,
    forbid_inline_seeds,
    require_harm_categories,
    require_inline_seeds,
    require_min_size,
    require_nonempty,
    require_seed_type,
    restrict_dataset_names,
)
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.scenario_strategy import ScenarioCompositeStrategy, ScenarioStrategy
from pyrit.scenario.core.scenario_target_defaults import get_default_adversarial_target, get_default_scorer_target

__all__ = [
    "AtomicAttack",
    "AttackTechnique",
    "AttackTechniqueFactory",
    "BaselineAttackPolicy",
    "DatasetAttackConfiguration",
    "DatasetConfiguration",
    "DatasetConstraintError",
    "DatasetObjectiveConfiguration",
    "DatasetPromptConfiguration",
    "DatasetSourceKind",
    "INLINE_DATASET_NAME",
    "Parameter",
    "ResolvedDataset",
    "forbid_inline_seeds",
    "require_harm_categories",
    "require_inline_seeds",
    "require_min_size",
    "require_nonempty",
    "require_seed_type",
    "restrict_dataset_names",
    "Scenario",
    "ScenarioCompositeStrategy",
    "ScenarioStrategy",
    "ScorerOverridePolicy",
    "get_default_scorer_target",
    "get_default_adversarial_target",
]
