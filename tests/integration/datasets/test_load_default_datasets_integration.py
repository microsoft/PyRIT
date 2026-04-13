# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration test for the LoadDefaultDatasets initializer.

Runs the full pipeline: discovers scenario default datasets, fetches them
from real remote sources, and stores them in in-memory CentralMemory.
"""

import logging

import pytest

from pyrit.memory import CentralMemory
from pyrit.setup.initializers.scenarios.load_default_datasets import LoadDefaultDatasets

logger = logging.getLogger(__name__)


class TestLoadDefaultDatasetsIntegration:
    """Integration test that LoadDefaultDatasets loads real datasets into memory."""

    @pytest.mark.asyncio
    async def test_initialize_loads_datasets_into_memory(self, sqlite_instance):
        """
        Verify that LoadDefaultDatasets.initialize_async() successfully fetches
        real datasets and stores them in CentralMemory.
        """
        initializer = LoadDefaultDatasets()
        await initializer.initialize_async()

        memory = CentralMemory.get_memory_instance()
        dataset_names = memory.get_seed_dataset_names()

        assert len(dataset_names) > 0, "No datasets were loaded into memory"
        logger.info(f"LoadDefaultDatasets loaded {len(dataset_names)} datasets into memory")
