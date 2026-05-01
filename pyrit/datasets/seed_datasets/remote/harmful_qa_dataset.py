# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _HarmfulQADataset(_RemoteDatasetLoader):
    """
    Loader for the HarmfulQA dataset from HuggingFace.

    HarmfulQA contains approximately 2k harmful questions organized by academic topic
    and subtopic, designed to test LLM susceptibility to harm-inducing question-answering.

    References:
        - https://huggingface.co/datasets/declare-lab/HarmfulQA
        - [@chu2023harmfulqa]
    License: Apache 2.0

    Warning: This dataset contains harmful questions designed to test LLM safety.
    """

    HF_DATASET_NAME: str = "declare-lab/HarmfulQA"

    def __init__(
        self,
        *,
        split: str = "train",
    ) -> None:
        """
        Initialize the HarmfulQA dataset loader.

        Args:
            split: Dataset split to load. Defaults to "train".
        """
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "harmful_qa"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch HarmfulQA dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the HarmfulQA questions.
        """
        logger.info(f"Loading HarmfulQA dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.HF_DATASET_NAME,
            split=self.split,
            cache=cache,
        )

        authors = [
            "Rishabh Bhardwaj",
            "Soujanya Poria",
        ]
        description = (
            "HarmfulQA contains ~2k harmful questions organized by academic topic and subtopic, "
            "designed to test LLM susceptibility to harm-inducing question-answering. Introduced "
            "in 'Red-Teaming Large Language Models using Chain of Utterances for Safety Alignment' (2023)."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["DeCLaRe Lab, Singapore University of Technology and Design"]

        seed_prompts = [
            SeedPrompt(
                value=item["question"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=[item["topic"]] if item.get("topic") else [],
                description=description,
                source=source_url,
                authors=authors,
                groups=groups,
                metadata={"subtopic": subtopic} if (subtopic := item.get("subtopic")) else {},
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} questions from HarmfulQA dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
