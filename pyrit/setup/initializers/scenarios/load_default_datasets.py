# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario Basic Dataset Loader.

If you don't have a database already, this can enable you to run all scenarios using
the pre-defined datasets in PyRIT. These are meant as a starting point only.
"""

import logging
import textwrap
from typing import List

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.registry import ScenarioRegistry
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


class LoadDefaultDatasets(PyRITInitializer):
    """Load default datasets for all registered scenarios."""

    @property
    def name(self) -> str:
        """Return the name of this initializer."""
        return "Default Dataset Loader for Scenarios"

    @property
    def execution_order(self) -> int:
        """Should be executed after most initializers."""
        return 10

    @property
    def description(self) -> str:
        """Return a description of this initializer."""
        return textwrap.dedent(
            """
                This configuration uses the DatasetLoader to load default datasets into memory.
                This will enable all scenarios to run. Datasets can be customized in memory.

                Note: if you are using persistent memory, avoid calling this every time as datasets
                can take time to load.
            """
        ).strip()

    @property
    def required_env_vars(self) -> List[str]:
        """Return the list of required environment variables."""
        return []

    async def initialize_async(self) -> None:
        """Load default datasets from all registered scenarios."""
        # Get ScenarioRegistry to discover all scenarios
        registry = ScenarioRegistry.get_registry_singleton()

        # Collect all default datasets from all scenarios
        all_default_datasets: List[str] = []

        # Get all scenario names from registry
        scenario_names = registry.get_names()

        for scenario_name in scenario_names:
            scenario_class = registry.get_class(scenario_name)
            if scenario_class:
                # Get default_dataset_config from the scenario class
                try:
                    datasets = scenario_class.default_dataset_config().get_default_dataset_names()
                    all_default_datasets.extend(datasets)
                    logger.info(f"Scenario '{scenario_name}' uses datasets: {datasets}")
                except Exception as e:
                    logger.warning(f"Could not get default datasets from scenario '{scenario_name}': {e}")

        # Remove duplicates
        unique_datasets = list(dict.fromkeys(all_default_datasets))

        if not unique_datasets:
            logger.warning("No datasets required by any scenario")
            return

        logger.info(f"Loading {len(unique_datasets)} unique datasets required by all scenarios")

        # Fetch the datasets
        dataset_list = await SeedDatasetProvider.fetch_datasets_async(
            dataset_names=unique_datasets,
        )

        # Store datasets in CentralMemory
        memory = CentralMemory.get_memory_instance()
        await memory.add_seed_datasets_to_memory_async(datasets=dataset_list, added_by="LoadDefaultDatasets")

        logger.info(f"Successfully loaded {len(dataset_list)} datasets into CentralMemory")
