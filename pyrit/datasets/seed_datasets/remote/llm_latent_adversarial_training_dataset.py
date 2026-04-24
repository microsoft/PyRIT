# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _LLMLatentAdversarialTrainingDataset(_RemoteDatasetLoader):
    """
    Loader for the LLM-LAT harmful dataset.

    This dataset contains prompts used to assess and analyze harmful behaviors
    in large language models.

    Reference: [@sheshadri2024lat]
    """

    def __init__(
        self,
        *,
        source: str = "LLM-LAT/harmful-dataset",
    ):
        """
        Initialize the LLM-LAT harmful dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "LLM-LAT/harmful-dataset".
        """
        self.source = source

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "llm_lat_harmful"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch LLM-LAT harmful dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the harmful prompts.
        """
        logger.info(f"Loading LLM-LAT harmful dataset from {self.source}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.source,
            config="default",
            split="train",
            cache=cache,
        )

        seed_prompts = [
            SeedPrompt(
                value=item["prompt"],
                data_type="text",
                dataset_name=self.dataset_name,
                description="This dataset contains prompts used to assess and analyze harmful behaviors in llm",
                source=f"https://huggingface.co/datasets/{self.source}",
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from LLM-LAT harmful dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
