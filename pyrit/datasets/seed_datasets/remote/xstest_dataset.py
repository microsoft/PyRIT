# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _XSTestDataset(_RemoteDatasetLoader):
    """
    Loader for the XSTest dataset.

    This dataset contains prompts designed to test exaggerated safety behaviors in language models.

    Reference: https://github.com/paul-rottger/exaggerated-safety
    """

    def __init__(
        self,
        *,
        source: str = "https://raw.githubusercontent.com/paul-rottger/exaggerated-safety/a3bb396/xstest_v2_prompts.csv",
        source_type: Literal["public_url", "file"] = "public_url",
    ):
        """
        Initialize the XSTest dataset loader.

        Args:
            source: URL to the XSTest CSV file. Defaults to the official repository.
            source_type: The type of source ('public_url' or 'file').
        """
        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xstest"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch XSTest dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the XSTest examples.
        """
        logger.info(f"Loading XSTest dataset from {self.source}")

        examples = self._fetch_from_url(
            source=self.source,
            source_type=self.source_type,
            cache=cache,
        )

        seed_prompts = [
            SeedPrompt(
                value=example["prompt"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=[example["note"]],
                description="A dataset of XSTest examples containing various categories such as violence, drugs, etc.",
                source=self.source,
            )
            for example in examples
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from XSTest dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
