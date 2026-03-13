# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset configuration for scenarios.

This module provides the DatasetConfiguration class that allows scenarios to be configured
with either explicit SeedGroups or dataset names (mutually exclusive).
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

from pyrit.memory import CentralMemory
from pyrit.models import SeedAttackGroup, SeedGroup

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models.seeds.seed import Seed
    from pyrit.scenario.core.scenario_strategy import ScenarioCompositeStrategy

# Key used when seed_groups are provided directly (not from a named dataset)
EXPLICIT_SEED_GROUPS_KEY = "_explicit_seed_groups"


class DatasetConfiguration:
    """
    Configuration for scenario datasets.

    This class provides a unified way to specify the dataset source for scenarios.
    Only ONE of `seed_groups` or `dataset_names` can be set.

    Args:
        seed_groups (Optional[List[SeedGroup]]): Explicit list of SeedGroup to use.
        dataset_names (Optional[List[str]]): Names of datasets to load from memory.
        max_dataset_size (Optional[int]): If set, randomly samples up to this many SeedGroups
            from the configured dataset source (without replacement, so no duplicates).
        scenario_composites (Optional[Sequence[ScenarioCompositeStrategy]]): The scenario
            strategies being executed. Subclasses can use this to filter or customize
            which seed groups are loaded based on the selected strategies.
    """

    def __init__(
        self,
        *,
        seed_groups: Optional[list[SeedGroup]] = None,
        dataset_names: Optional[list[str]] = None,
        max_dataset_size: Optional[int] = None,
        scenario_composites: Optional[Sequence[ScenarioCompositeStrategy]] = None,
    ) -> None:
        """
        Initialize a DatasetConfiguration.

        Args:
            seed_groups (Optional[List[SeedGroup]]): Explicit list of SeedGroup to use.
            dataset_names (Optional[List[str]]): Names of datasets to load from memory.
            max_dataset_size (Optional[int]): If set, randomly samples up to this many SeedGroups
                (without replacement).
            scenario_composites (Optional[Sequence[ScenarioCompositeStrategy]]): The scenario
                strategies being executed. Subclasses can use this to filter or customize
                which seed groups are loaded.

        Raises:
            ValueError: If both seed_groups and dataset_names are set.
            ValueError: If max_dataset_size is less than 1.
        """
        # Validate that only one data source is set
        if seed_groups is not None and dataset_names is not None:
            raise ValueError(
                "Only one of 'seed_groups' or 'dataset_names' can be set. "
                "Use 'seed_groups' to provide explicit SeedGroups, "
                "or 'dataset_names' to load from memory."
            )

        if max_dataset_size is not None and max_dataset_size < 1:
            raise ValueError("'max_dataset_size' must be a positive integer (>= 1).")

        # Store private attributes
        self._seed_groups = list(seed_groups) if seed_groups is not None else None
        self.max_dataset_size = max_dataset_size
        self._dataset_names = list(dataset_names) if dataset_names is not None else None
        self._scenario_composites = scenario_composites

    def get_seed_groups(self) -> dict[str, list[SeedGroup]]:
        """
        Resolve and return seed groups based on the configuration.

        This method handles all resolution logic:
        1. If seed_groups is set, use those directly (under key '_explicit_seed_groups')
        2. If dataset_names is set, load from memory using those names

        In all cases, max_dataset_size is applied **per dataset** if set.

        Subclasses can override this to filter or customize which seed groups
        are loaded based on the stored scenario_composites.

        Returns:
            Dict[str, List[SeedGroup]]: Dictionary mapping dataset names to their
                seed groups. When explicit seed_groups are provided, the key is
                '_explicit_seed_groups'. Each dataset's seed groups are potentially
                sampled down to max_dataset_size.

        Raises:
            ValueError: If no seed groups could be resolved from the configuration.
        """
        result: dict[str, list[SeedGroup]] = {}

        if self._seed_groups is not None:
            # Use explicit seed groups under a special key
            sampled = self._apply_max_dataset_size(list(self._seed_groups))
            if sampled:
                result[EXPLICIT_SEED_GROUPS_KEY] = sampled
        elif self._dataset_names is not None:
            # Load from specified dataset names, applying max per dataset
            for name in self._dataset_names:
                if name == EXPLICIT_SEED_GROUPS_KEY:
                    raise ValueError(
                        f"Dataset name '{EXPLICIT_SEED_GROUPS_KEY}' is reserved for internal use. "
                        "Please rename your dataset."
                    )
                loaded = self._load_seed_groups_for_dataset(dataset_name=name)
                if loaded:
                    sampled = self._apply_max_dataset_size(loaded)
                    result[name] = sampled

        if not result:
            raise ValueError("DatasetConfiguration has no seed_groups. Set seed_groups or dataset_names.")

        return result

    def _load_seed_groups_for_dataset(self, *, dataset_name: str) -> list[SeedGroup]:
        """
        Load seed groups for a single dataset from memory.

        Override this method in subclasses to customize how seed groups are loaded
        from memory. The default implementation loads by exact dataset name.

        Args:
            dataset_name (str): The name of the dataset to load.

        Returns:
            List[SeedGroup]: Seed groups loaded from memory, or empty list if none found.
        """
        memory = CentralMemory.get_memory_instance()
        return list(memory.get_seed_groups(dataset_name=dataset_name) or [])

    def get_all_seed_groups(self) -> list[SeedGroup]:
        """
        Resolve and return all seed groups as a flat list.

        This is a convenience method that calls get_seed_groups() and flattens
        the results into a single list. Use this when you don't need to track
        which dataset each seed group came from.

        Returns:
            List[SeedGroup]: All resolved seed groups from all datasets,
                with max_dataset_size applied per dataset.

        Raises:
            ValueError: If no seed groups could be resolved from the configuration.
        """
        seed_groups_by_dataset = self.get_seed_groups()
        all_groups: list[SeedGroup] = []
        for groups in seed_groups_by_dataset.values():
            all_groups.extend(groups)
        return all_groups

    def get_seed_attack_groups(self) -> dict[str, list[SeedAttackGroup]]:
        """
        Resolve and return seed groups as SeedAttackGroups, grouped by dataset.

        This wraps get_seed_groups() and converts each SeedGroup to a SeedAttackGroup.
        Use this when you need attack-specific functionality like objectives,
        prepended conversations, or simulated conversation configuration.

        Returns:
            Dict[str, List[SeedAttackGroup]]: Dictionary mapping dataset names to their
                seed attack groups.

        Raises:
            ValueError: If no seed groups could be resolved from the configuration.
        """
        seed_groups_by_dataset = self.get_seed_groups()
        result: dict[str, list[SeedAttackGroup]] = {}
        for dataset_name, groups in seed_groups_by_dataset.items():
            result[dataset_name] = [SeedAttackGroup(seeds=list(sg.seeds)) for sg in groups]
        return result

    def get_all_seed_attack_groups(self) -> list[SeedAttackGroup]:
        """
        Resolve and return all seed groups as SeedAttackGroups in a flat list.

        This is a convenience method that calls get_seed_attack_groups() and flattens
        the results into a single list. Use this for attack scenarios that need
        SeedAttackGroup functionality.

        Returns:
            List[SeedAttackGroup]: All resolved seed attack groups from all datasets.

        Raises:
            ValueError: If no seed groups could be resolved from the configuration.
        """
        attack_groups_by_dataset = self.get_seed_attack_groups()
        all_groups: list[SeedAttackGroup] = []
        for groups in attack_groups_by_dataset.values():
            all_groups.extend(groups)
        return all_groups

    def get_default_dataset_names(self) -> list[str]:
        """
        Get the list of default dataset names for this configuration.

        This is used by the CLI to display what datasets the scenario uses by default.

        Returns:
            List[str]: List of dataset names, or empty list if using explicit seed_groups.
        """
        if self._dataset_names is not None:
            return list(self._dataset_names)
        return []

    def _apply_max_dataset_size(self, seed_groups: list[SeedGroup]) -> list[SeedGroup]:
        """
        Apply max_dataset_size sampling to a list of seed groups.

        Uses random sampling without replacement (no duplicates in the result).

        Args:
            seed_groups (List[SeedGroup]): The seed groups to potentially sample from.

        Returns:
            List[SeedGroup]: The original list if max_dataset_size is not set,
                or a random sample of up to max_dataset_size unique items.
        """
        if self.max_dataset_size is None or len(seed_groups) <= self.max_dataset_size:
            return seed_groups
        return random.sample(seed_groups, self.max_dataset_size)

    def has_data_source(self) -> bool:
        """
        Check if this configuration has a data source configured.

        Returns:
            bool: True if seed_groups or dataset_names is configured.
        """
        return self._seed_groups is not None or self._dataset_names is not None

    def get_all_seeds(self) -> list[Seed]:
        """
        Load all seed prompts from memory for all configured datasets.

        This is a convenience method that retrieves SeedPrompt objects directly
        from memory for all configured datasets. If max_dataset_size is set, randomly
        samples up to that many prompts per dataset (without replacement).

        Returns:
            List[SeedPrompt]: List of SeedPrompt objects from all configured datasets.
                Returns an empty list if no prompts are found.

        Raises:
            ValueError: If no dataset names are configured.
        """
        if self._dataset_names is None:
            raise ValueError("No dataset names configured. Set dataset_names to use get_all_seed_prompts.")

        memory = CentralMemory.get_memory_instance()
        all_seeds: list[Seed] = []

        for dataset_name in self._dataset_names:
            seeds = memory.get_seeds(dataset_name=dataset_name)

            # Apply max_dataset_size sampling per dataset if configured
            if self.max_dataset_size is not None and len(seeds) > self.max_dataset_size:
                seeds = random.sample(seeds, self.max_dataset_size)
            all_seeds.extend(seeds)

        return all_seeds
