# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedObjective

logger = logging.getLogger(__name__)


class _CategoricalHarmfulQADataset(_RemoteDatasetLoader):
    """
    Loader for the CategoricalHarmfulQA (CatQA) dataset from HuggingFace.

    CatQA contains 550 harmful questions hand-authored against the combined list of
    prohibited use cases from OpenAI's usage policies and Meta's Llama2 acceptable
    use policy. Questions are organized across 11 main harm categories, each split
    into 5 sub-categories with 10 questions per sub-category. The dataset is
    available in English ("en"), Chinese ("zh"), and Vietnamese ("vi") splits;
    translations were produced by an unaligned LLM and refined by human annotators.

    References:
        - https://huggingface.co/datasets/declare-lab/CategoricalHarmfulQA
        - [@bhardwaj2024homer]
    License: Apache 2.0

    Warning: This dataset contains harmful questions designed to evaluate LLM safety,
    including content related to child abuse, weapons, and other deeply harmful topics.
    """

    HF_DATASET_NAME: str = "declare-lab/CategoricalHarmfulQA"

    # Metadata
    harm_categories: list[str] = [
        "adult content",
        "child abuse",
        "economic harm",
        "fraud/deception",
        "hate/harass/violence",
        "illegal activity",
        "malware viruses",
        "physical harm",
        "political campaigning",
        "privacy violation activity",
        "tailored financial advice",
    ]
    modalities: list[str] = ["text"]
    size: str = "large"  # 550 questions per language split
    tags: set[str] = {"safety", "multilingual"}

    def __init__(
        self,
        *,
        language: Literal["en", "zh", "vi"] = "en",
    ) -> None:
        """
        Initialize the CategoricalHarmfulQA dataset loader.

        Args:
            language (Literal["en", "zh", "vi"]): Language split to load.
                "en" (English), "zh" (Chinese), or "vi" (Vietnamese). Defaults to "en".
        """
        self.language: Literal["en", "zh", "vi"] = language

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "categorical_harmful_qa"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch CategoricalHarmfulQA dataset from HuggingFace and return as SeedDataset.

        The Category field is exposed via ``harm_categories``; the Subcategory and
        the language code are recorded in the per-objective ``metadata``.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the CategoricalHarmfulQA objectives.
        """
        logger.info(f"Loading CategoricalHarmfulQA dataset from {self.HF_DATASET_NAME} (language={self.language})")

        data = await self._fetch_from_huggingface(
            dataset_name=self.HF_DATASET_NAME,
            split=self.language,
            cache=cache,
        )

        authors = [
            "Rishabh Bhardwaj",
            "Do Duc Anh",
            "Soujanya Poria",
        ]
        description = (
            "CategoricalHarmfulQA (CatQA) contains 550 hand-authored harmful questions "
            "organized into 11 main harm categories and 5 sub-categories each, framed "
            "against OpenAI's prohibited use cases and Meta's Llama2 acceptable use policy. "
            "Introduced in 'Language Models are Homer Simpson! Safety Re-Alignment of "
            "Fine-tuned Language Models through Task Arithmetic' (2024). Available in "
            "English, Chinese, and Vietnamese."
        )
        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["DeCLaRe Lab, Singapore University of Technology and Design"]

        seed_objectives = [
            SeedObjective(
                value=item["Question"],
                name="CategoricalHarmfulQA",
                dataset_name=self.dataset_name,
                harm_categories=[item["Category"]] if item.get("Category") else [],
                description=description,
                source=source_url,
                authors=authors,
                groups=groups,
                metadata={
                    "language": self.language,
                    **({"subcategory": subcategory} if (subcategory := item.get("Subcategory")) else {}),
                },
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_objectives)} objectives from CategoricalHarmfulQA dataset")

        return SeedDataset(seeds=seed_objectives, dataset_name=self.dataset_name)
