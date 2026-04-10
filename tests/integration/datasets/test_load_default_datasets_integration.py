# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration test for the LoadDefaultDatasets initializer.

Runs the full pipeline: discovers scenario default datasets, fetches them
from real remote sources, and stores them in in-memory CentralMemory.
"""

import logging

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.setup.initializers.scenarios.load_default_datasets import LoadDefaultDatasets

logger = logging.getLogger(__name__)


class TestLoadDefaultDatasetsIntegration:
    """Integration test that LoadDefaultDatasets loads real datasets into memory."""

    @pytest.mark.asyncio
    async def test_initialize_loads_datasets_into_memory(self):
        """
        Verify that LoadDefaultDatasets.initialize_async() successfully fetches
        real datasets and stores them in CentralMemory.
        """
        initializer = LoadDefaultDatasets()
        await initializer.initialize_async()

        memory = CentralMemory.get_memory_instance()
        seed_datasets = await memory.get_seed_datasets_async()

        assert len(seed_datasets) > 0, "No datasets were loaded into memory"
        logger.info(f"LoadDefaultDatasets loaded {len(seed_datasets)} datasets into memory")

        # Verify basic structure of loaded datasets
        for dataset in seed_datasets:
            assert dataset.dataset_name, "Loaded dataset has no name"
            assert len(dataset.seeds) > 0, f"Dataset '{dataset.dataset_name}' has no seeds"

    @pytest.mark.asyncio
    async def test_all_scenario_datasets_are_fetchable(self):
        """
        Verify that every dataset name referenced by registered scenarios
        can actually be fetched from SeedDatasetProvider.
        """
        from pyrit.registry import ScenarioRegistry

        registry = ScenarioRegistry.get_registry_singleton()
        scenario_names = registry.get_names()

        all_dataset_names: list[str] = []
        for scenario_name in scenario_names:
            scenario_class = registry.get_class(scenario_name)
            if scenario_class:
                try:
                    datasets = scenario_class.default_dataset_config().get_default_dataset_names()
                    all_dataset_names.extend(datasets)
                except Exception as e:
                    logger.warning(f"Could not get default datasets from scenario '{scenario_name}': {e}")

        unique_names = list(dict.fromkeys(all_dataset_names))
        assert len(unique_names) > 0, "No scenarios registered any default datasets"

        fetched = await SeedDatasetProvider.fetch_datasets_async(dataset_names=unique_names)
        assert len(fetched) == len(unique_names), (
            f"Expected {len(unique_names)} datasets but fetched {len(fetched)}. "
            f"Missing: {set(unique_names) - {d.dataset_name for d in fetched}}"
        )
