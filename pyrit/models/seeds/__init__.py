# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Seeds module - Contains all seed-related classes for PyRIT.

This module provides the core seed types used throughout PyRIT:
- Seed: Base class for all seed types
- SeedPrompt: Seed with role and sequence for conversations
- SeedObjective: Seed representing an attack objective
- SeedGroup: Base container for grouping seeds
- SeedAttackGroup: Attack-specific seed group with objectives and prepended conversations
- SeedAttackTechniqueGroup: Technique-specific seed group where all seeds must be general strategies
- SeedSimulatedConversation: Configuration for generating simulated conversations
- SeedDataset: Container for managing collections of seeds
"""

from pyrit.models.seeds.seed import Seed
from pyrit.models.seeds.seed_attack_group import SeedAttackGroup
from pyrit.models.seeds.seed_attack_technique_group import SeedAttackTechniqueGroup
from pyrit.models.seeds.seed_dataset import SeedDataset
from pyrit.models.seeds.seed_group import SeedGroup, SeedUnion
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt
from pyrit.models.seeds.seed_simulated_conversation import (
    NextMessageSystemPromptPaths,
    SeedSimulatedConversation,
    SimulatedTargetSystemPromptPaths,
)
from pyrit.models.seeds.yaml_seed_loader import (
    load_seed_dataset_from_yaml,
    load_seed_from_yaml,
    load_seed_prompt_from_yaml_with_required_parameters,
)

__all__ = [
    "load_seed_dataset_from_yaml",
    "load_seed_from_yaml",
    "load_seed_prompt_from_yaml_with_required_parameters",
    "NextMessageSystemPromptPaths",
    "Seed",
    "SeedAttackGroup",
    "SeedAttackTechniqueGroup",
    "SeedDataset",
    "SeedGroup",
    "SeedObjective",
    "SeedPrompt",
    "SeedSimulatedConversation",
    "SeedUnion",
    "SimulatedTargetSystemPromptPaths",
]
