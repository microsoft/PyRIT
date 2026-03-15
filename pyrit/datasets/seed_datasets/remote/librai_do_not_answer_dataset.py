# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _LibrAIDoNotAnswerDataset(_RemoteDatasetLoader):
    """
    Loader for the LibrAI 'Do Not Answer' dataset.

    This dataset contains questions across multiple risk areas and harm types
    to test LLM safety and refusal behaviors.

    Reference: [@wang2023donotanswer]
    GitHub: https://github.com/libr-ai/do-not-answer
    """

    def __init__(
        self,
        *,
        source: str = "LibrAI/do-not-answer",
    ):
        """
        Initialize the LibrAI Do Not Answer dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "LibrAI/do-not-answer".
        """
        self.source = source

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "librai_do_not_answer"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch LibrAI Do Not Answer dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the Do Not Answer prompts.
        """
        logger.info(f"Loading LibrAI Do Not Answer dataset from {self.source}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.source,
            split="train",
            cache=cache,
        )

        seed_prompts = [
            SeedPrompt(
                value=entry["question"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=[entry["risk_area"], entry["types_of_harm"], entry["specific_harms"]],
                description=(
                    f"This is a prompt from the 'Do Not Answer' dataset under the risk area: {entry['risk_area']}, "
                    f"harm type: {entry['types_of_harm']}, and specific harm: {entry['specific_harms']}."
                ),
                source=f"https://huggingface.co/datasets/{self.source}",
            )
            for entry in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from LibrAI Do Not Answer dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
