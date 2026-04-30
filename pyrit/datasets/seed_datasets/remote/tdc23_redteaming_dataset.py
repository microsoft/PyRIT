# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _TDC23RedteamingDataset(_RemoteDatasetLoader):
    """
    Loader for the TDC23-RedTeaming dataset.

    This dataset contains 100 prompts aimed at generating harmful content across multiple
    harm categories related to fairness, misinformation, dangerous and criminal activities,
    violence, etc. in the style of writing narratives.

    Reference: [@mazeika2023tdc]
    """

    def __init__(
        self,
        *,
        source: str = "walledai/TDC23-RedTeaming",
    ) -> None:
        """
        Initialize the TDC23-RedTeaming dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "walledai/TDC23-RedTeaming".
        """
        self.source = source

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "tdc23_redteaming"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch TDC23-RedTeaming dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the red-teaming prompts.
        """
        logger.info(f"Loading TDC23-RedTeaming dataset from {self.source}")

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
                description=(
                    "TDC23-RedTeaming dataset from HuggingFace, created by Walled AI. "
                    "Contains 100 prompts aimed at generating harmful content across multiple harm categories "
                    "related to fairness, misinformation, dangerous and criminal activities, violence, etc. "
                    "in the style of writing narratives."
                ),
                source=f"https://huggingface.co/datasets/{self.source}",
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from TDC23-RedTeaming dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
