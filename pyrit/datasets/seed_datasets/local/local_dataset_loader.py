# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from pathlib import Path
from typing import Any, Callable

from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.models import SeedDataset

logger = logging.getLogger(__name__)


class _LocalDatasetLoader(SeedDatasetProvider):
    """
    Loader for local YAML dataset files.

    This loader discovers and loads datasets from local YAML files.
    Each YAML file should be in the standard SeedDataset format.
    """

    should_register = False

    def __init__(self, *, file_path: Path):
        """
        Initialize the local dataset loader.

        Args:
            file_path: Path to the YAML dataset file.
        """
        self.file_path = file_path

        # Pre-load to get dataset name
        try:
            dataset = SeedDataset.from_yaml_file(file_path)
            # Use the dataset_name from the YAML if available, otherwise use filename
            self._dataset_name = (
                getattr(dataset, "dataset_name", None) or getattr(dataset, "name", None) or file_path.stem
            )
        except Exception as e:
            logger.warning(f"Could not pre-load dataset from {file_path}: {e}")
            self._dataset_name = file_path.stem

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return self._dataset_name

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Load the dataset from the local YAML file.

        Args:
            cache: Ignored for local datasets (included for interface consistency).

        Returns:
            SeedDataset: The loaded dataset.

        Raises:
            Exception: If the dataset cannot be loaded.
        """
        try:
            logger.info(f"Loading local dataset from {self.file_path}")
            dataset = SeedDataset.from_yaml_file(self.file_path)
            if not dataset.dataset_name:
                dataset.dataset_name = self.dataset_name
            return dataset
        except Exception as e:
            logger.error(f"Failed to load local dataset from {self.file_path}: {e}")
            raise


def _register_local_datasets() -> None:
    """
    Auto-discover and register all YAML files from the seed_datasets directory.
    """
    # Get the path to the seed_datasets directory (parent of this file)
    seed_datasets_path = Path(__file__).parent

    if seed_datasets_path.exists():
        # Search for both .prompt and .yaml files
        for pattern in ["**/*.prompt", "**/*.yaml"]:
            for yaml_file in seed_datasets_path.glob(pattern):
                try:
                    # Create a dynamic subclass for each file to register it
                    # The class name needs to be unique
                    class_name = f"LocalDataset_{yaml_file.stem.replace('-', '_').replace(' ', '_')}"

                    # Define the class dynamically
                    # We set should_register=True so it gets registered
                    # We override __init__ to pass the specific file_path

                    def make_init(path: Path) -> Callable[[Any], None]:
                        def __init__(self: Any) -> None:
                            super(self.__class__, self).__init__(file_path=path)

                        return __init__

                    type(
                        class_name,
                        (_LocalDatasetLoader,),
                        {"__init__": make_init(yaml_file), "should_register": True, "__module__": __name__},
                    )

                    logger.debug(f"Registered local dataset loader: {class_name} for {yaml_file.name}")
                except Exception as e:
                    logger.warning(f"Failed to register local dataset {yaml_file}: {e}")
    else:
        logger.warning(f"Seed datasets directory not found: {seed_datasets_path}")


# Execute registration
_register_local_datasets()
