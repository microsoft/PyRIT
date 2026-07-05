# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _SimpleSafetyTestsDataset(_RemoteDatasetLoader):
    """
    Loader for the SimpleSafetyTests dataset from HuggingFace.

    SimpleSafetyTests contains 100 critical safety test prompts designed as a lightweight
    diagnostic set for quickly evaluating the most basic safety properties of LLMs.

    References:
        - https://huggingface.co/datasets/Bertievidgen/SimpleSafetyTests
        - [@vidgen2023simplesafetytests]
    License: CC BY 4.0

    Warning: This dataset contains prompts related to harmful and unsafe content categories.
    """

    HF_DATASET_NAME: str = "Bertievidgen/SimpleSafetyTests"

    def __init__(
        self,
        *,
        split: str = "test",
    ) -> None:
        """
        Initialize the SimpleSafetyTests dataset loader.

        Args:
            split: Dataset split to load. Defaults to "test".
        """
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "simple_safety_tests"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch SimpleSafetyTests dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the SimpleSafetyTests prompts.
        """
        logger.info(f"Loading SimpleSafetyTests dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.HF_DATASET_NAME,
            split=self.split,
            cache=cache,
        )

        authors = [
            "Bertie Vidgen",
            "Nino Scherrer",
            "Hannah Rose Kirk",
            "Rebecca Qian",
            "Anand Kannappan",
            "Scott Niekum",
            "Paul Röttger",
        ]
        description = (
            "SimpleSafetyTests contains 100 critical safety test prompts designed as a lightweight "
            "diagnostic set for quickly evaluating the most basic safety properties of LLMs."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["Patronus AI", "University of Oxford", "Bocconi University"]
        harm_area_alias_overrides: dict[str, list[str]] = {
            "Suicide, Self-Harm, and Eating Disorders": ["SUICIDE", "SELF_HARM", "EATING_DISORDERS"],
            "Suicide, Self-Harm and Eating Disorders": ["SUICIDE", "SELF_HARM", "EATING_DISORDERS"],
            "Physical Harm": ["VIOLENT_CONTENT", "COORDINATION_HARM"],
            "Physical Harm and Violence": ["VIOLENT_CONTENT", "COORDINATION_HARM"],
            "Violence and Physical Harm": ["VIOLENT_CONTENT", "COORDINATION_HARM"],
            "Child Abuse": ["GROOMING", "SEXUAL_CONTENT", "CHILD_LEAKAGE"],
            "Illegal and Highly Regulated items": ["REGULATED_GOODS", "COORDINATION_HARM"],
            "Illegal Items": ["REGULATED_GOODS", "COORDINATION_HARM"],
            "Scams and Fraud": ["SCAMS", "DECEPTION"],
        }

        seed_prompts = []
        for item in data:
            # Standardize harm categories
            standardized_categories = self._standardize_harm_categories(
                item.get("harm_area"),
                alias_overrides=harm_area_alias_overrides,
            )

            metadata: dict[str, str | int] = {}
            for key, value in item.items():
                if key == "prompt" or value is None:
                    continue

                metadata[key] = value if isinstance(value, (str, int)) else str(value)

            seed_prompt = SeedPrompt(
                value=item["prompt"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=standardized_categories,
                description=description,
                source=source_url,
                authors=authors,
                groups=groups,
                metadata=metadata if metadata else None,
            )
            seed_prompts.append(seed_prompt)

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from SimpleSafetyTests dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
