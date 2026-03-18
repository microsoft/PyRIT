# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _EthicalRedTeamDataset(_RemoteDatasetLoader):
    """
    Loader for the Ethical Red Team dataset.

    This dataset contains prompts intended for red-teaming and safety testing of
    language models.
    """

    def __init__(
        self,
        *,
        source: str = "srushtisingh/Ethical_redteam",
        config: str = "default",
        split: str = "train",
    ):
        self.source = source
        self.config = config
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "ethical_redteam"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch Ethical Red Team dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the ethical red team prompts.
        """
        logger.info(f"Loading Ethical Red Team dataset from {self.source}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.source,
            config=self.config,
            split=self.split,
            cache=cache,
        )

        seed_prompts = [
            SeedPrompt(
                value=item["prompt"],
                data_type="text",
                dataset_name=self.dataset_name,
                description=(
                    "Ethical Red Team dataset from Hugging Face. "
                    "Contains prompts intended for red-teaming and safety testing of language models."
                ),
                source=f"https://huggingface.co/datasets/{self.source}",
            )
            for item in data
            if item.get("prompt")
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from Ethical Red Team dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
