# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated — use ``scenario_techniques`` instead.

This module re-exports everything from ``scenario_techniques`` for backward
compatibility.  It will be removed in a future release.
"""

from pyrit.scenario.core.scenario_techniques import (
    SCENARIO_TECHNIQUES as CORE_TECHNIQUES,
    ScenarioTechniqueRegistrar as CoreTechniqueRegistrar,
    get_default_adversarial_target,
)

# Re-export TechniqueSpec from its canonical location
from pyrit.registry.object_registries.attack_technique_registry import TechniqueSpec

__all__ = [
    "CORE_TECHNIQUES",
    "CoreTechniqueRegistrar",
    "TechniqueSpec",
    "get_default_adversarial_target",
]
