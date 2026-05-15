# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Core scenario classes for running attack configurations."""

from pyrit.common.parameter import Parameter
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory, ScorerOverridePolicy
from pyrit.scenario.core.dataset_configuration import EXPLICIT_SEED_GROUPS_KEY, DatasetConfiguration
from pyrit.scenario.core.scenario import BaselinePolicy, Scenario
from pyrit.scenario.core.scenario_strategy import ScenarioCompositeStrategy, ScenarioStrategy
from pyrit.scenario.core.scenario_target_defaults import get_default_adversarial_target, get_default_scorer_target
from pyrit.scenario.core.scenario_techniques import (
    SCENARIO_TECHNIQUES,
    register_scenario_techniques,
)

__all__ = [
    "AtomicAttack",
    "AttackTechnique",
    "AttackTechniqueFactory",
    "BaselinePolicy",
    "DatasetConfiguration",
    "EXPLICIT_SEED_GROUPS_KEY",
    "SCENARIO_TECHNIQUES",
    "Parameter",
    "Scenario",
    "ScenarioCompositeStrategy",
    "ScenarioStrategy",
    "ScorerOverridePolicy",
    "register_scenario_techniques",
    "get_default_scorer_target",
    "get_default_adversarial_target",
]
