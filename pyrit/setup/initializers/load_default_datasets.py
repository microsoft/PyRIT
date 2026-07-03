# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario dataset loader.

If you don't have a database already, this can enable you to run scenarios
using the pre-defined datasets in PyRIT. These are meant as a starting point
only.
"""

import logging
import textwrap

from pyrit.datasets import SeedDatasetFilter, SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.models.parameter import Parameter
from pyrit.registry import ScenarioRegistry
from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


class LoadDefaultDatasets(PyRITInitializer):
    """
    Load datasets into memory so scenarios can run.

    By default this loads the datasets required by all registered scenarios.
    Pass ``dataset_names`` to load specific datasets by name, or ``tags`` to
    select datasets by metadata.
    """

    @property
    def description(self) -> str:
        """A description of this initializer."""
        return textwrap.dedent(
            """
                Loads datasets into memory so scenarios can run. By default loads the datasets
                required by all registered scenarios; use the dataset_names or tags parameters to
                select datasets explicitly.

                Note: if you are using persistent memory, avoid calling this every time as datasets
                can take time to load.
            """
        ).strip()

    @property
    def required_env_vars(self) -> list[str]:
        """The list of required environment variables."""
        return []

    @property
    def supported_parameters(self) -> list[Parameter]:
        """The list of parameters this initializer accepts."""
        return [
            Parameter(
                name="dataset_names",
                description="Explicit dataset names to load. Overrides the scenario-default selection.",
                default=[],
            ),
            Parameter(
                name="tags",
                description="Load datasets whose metadata matches these tags. Overrides scenario-default selection.",
                default=[],
            ),
        ]

    async def initialize_async(self) -> None:
        """Resolve the dataset selection and load it into CentralMemory."""
        dataset_names = self.params.get("dataset_names", [])
        tags = self.params.get("tags", [])

        if dataset_names:
            unique_datasets = list(dict.fromkeys(dataset_names))
            logger.info(f"Loading {len(unique_datasets)} explicitly requested dataset(s)")
        elif tags:
            matched = await SeedDatasetProvider.get_all_dataset_names_async(filters=SeedDatasetFilter(tags=set(tags)))
            unique_datasets = list(dict.fromkeys(matched))
            logger.info(f"Loading {len(unique_datasets)} dataset(s) matching tags: {sorted(tags)}")
        else:
            unique_datasets = self._scenario_default_dataset_names()
            logger.info(f"Loading {len(unique_datasets)} unique datasets required by all scenarios")

        if not unique_datasets:
            logger.warning("No datasets matched the requested selection")
            return

        dataset_list = await SeedDatasetProvider.fetch_datasets_async(
            dataset_names=unique_datasets,
        )

        memory = CentralMemory.get_memory_instance()
        await memory.add_seed_datasets_to_memory_async(datasets=dataset_list, added_by="LoadDefaultDatasets")

        logger.info(f"Successfully loaded {len(dataset_list)} datasets into CentralMemory")

    @staticmethod
    def _scenario_default_dataset_names() -> list[str]:
        """
        Collect the deduplicated default dataset names across all registered scenarios.

        Returns:
            list[str]: The deduplicated dataset names required by all registered scenarios.
        """
        registry = ScenarioRegistry.get_registry_singleton()

        all_default_datasets: list[str] = []
        for metadata in registry.get_all_registered_class_metadata():
            datasets = list(metadata.default_datasets)
            all_default_datasets.extend(datasets)
            logger.info(f"Scenario '{metadata.registry_name}' uses datasets: {datasets}")

        return list(dict.fromkeys(all_default_datasets))
