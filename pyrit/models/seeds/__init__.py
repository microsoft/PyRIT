# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Seeds module - Contains all seed-related classes for PyRIT.

This module provides the core seed types used throughout PyRIT:
- Seed: Base class for all seed types
- SeedPrompt: Seed with role and sequence for conversations
- SeedObjective: Seed representing an attack objective
- SeedGroup: Base container for grouping seeds
- AttackSeedGroup: Attack-specific seed group with objectives and prepended conversations
- AttackTechniqueSeedGroup: Technique-specific seed group where all seeds must be general strategies
- SeedSimulatedConversation: Configuration for generating simulated conversations
- SeedDataset: Container for managing collections of seeds
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models.seeds.attack_seed_group import AttackSeedGroup
    from pyrit.models.seeds.attack_technique_seed_group import AttackTechniqueSeedGroup
    from pyrit.models.seeds.seed import Seed
    from pyrit.models.seeds.seed_dataset import SeedDataset
    from pyrit.models.seeds.seed_group import SeedGroup, SeedUnion
    from pyrit.models.seeds.seed_grouping import group_seeds_into_attack_groups
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

_LAZY_EXPORTS: dict[str, str] = {
    "load_seed_dataset_from_yaml": "pyrit.models.seeds.yaml_seed_loader",
    "load_seed_from_yaml": "pyrit.models.seeds.yaml_seed_loader",
    "load_seed_prompt_from_yaml_with_required_parameters": "pyrit.models.seeds.yaml_seed_loader",
    "group_seeds_into_attack_groups": "pyrit.models.seeds.seed_grouping",
    "NextMessageSystemPromptPaths": "pyrit.models.seeds.seed_simulated_conversation",
    "Seed": "pyrit.models.seeds.seed",
    "AttackSeedGroup": "pyrit.models.seeds.attack_seed_group",
    "AttackTechniqueSeedGroup": "pyrit.models.seeds.attack_technique_seed_group",
    "SeedDataset": "pyrit.models.seeds.seed_dataset",
    "SeedGroup": "pyrit.models.seeds.seed_group",
    "SeedObjective": "pyrit.models.seeds.seed_objective",
    "SeedPrompt": "pyrit.models.seeds.seed_prompt",
    "SeedSimulatedConversation": "pyrit.models.seeds.seed_simulated_conversation",
    "SeedUnion": "pyrit.models.seeds.seed_group",
    "SimulatedTargetSystemPromptPaths": "pyrit.models.seeds.seed_simulated_conversation",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    """
    Resolve a public seed export on first access.

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
