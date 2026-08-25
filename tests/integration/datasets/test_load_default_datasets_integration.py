# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration test for the LoadDefaultDatasets initializer.

Runs the full pipeline with a bounded representative selection and stores the
datasets in in-memory CentralMemory.
"""

import logging

from pyrit.memory import CentralMemory
from pyrit.setup.initializers.load_default_datasets import LoadDefaultDatasets

logger = logging.getLogger(__name__)

BOUNDED_DATASET_NAMES = [
    "garak_package_hallucination_real_tasks",
    "garak_package_hallucination_stubs",
    "garak_package_hallucination_unreal_tasks",
]


class TestLoadDefaultDatasetsIntegration:
    """Integration test that LoadDefaultDatasets loads real datasets into memory."""

    async def test_initialize_loads_datasets_into_memory(self, sqlite_instance):
        """
        Verify that LoadDefaultDatasets.initialize_async() successfully fetches
        real datasets and stores them in CentralMemory.
        """
        initializer = LoadDefaultDatasets()
        initializer.params = {"dataset_names": BOUNDED_DATASET_NAMES}
        await initializer.initialize_async()

        memory = CentralMemory.get_memory_instance()
        dataset_names = set(memory.get_seed_dataset_names())

        assert dataset_names == set(BOUNDED_DATASET_NAMES)
        logger.info(f"LoadDefaultDatasets loaded {len(dataset_names)} datasets into memory")
