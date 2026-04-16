# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Core scenario classes for running attack configurations."""

from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.scenario_techniques import (
    SCENARIO_TECHNIQUES,
    get_default_adversarial_target,
    register_scenario_techniques,
)
from pyrit.scenario.core.dataset_configuration import EXPLICIT_SEED_GROUPS_KEY, DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_strategy import ScenarioCompositeStrategy, ScenarioStrategy

# TechniqueSpec lives in the registry module but is re-exported here for convenience
from pyrit.registry.object_registries.attack_technique_registry import TechniqueSpec

__all__ = [
    "AtomicAttack",
    "AttackTechnique",
    "AttackTechniqueFactory",
    "DatasetConfiguration",
    "EXPLICIT_SEED_GROUPS_KEY",
    "SCENARIO_TECHNIQUES",
    "Scenario",
    "ScenarioCompositeStrategy",
    "ScenarioStrategy",
    "TechniqueSpec",
    "get_default_adversarial_target",
    "register_scenario_techniques",
]
