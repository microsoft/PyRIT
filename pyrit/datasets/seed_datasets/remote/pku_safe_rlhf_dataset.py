# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal, Optional

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _PKUSafeRLHFDataset(_RemoteDatasetLoader):
    """
    Loader for the PKU-SafeRLHF dataset.

    This dataset contains prompts with RLHF markers for unsafe responses across
    multiple harm categories.

    Reference: https://huggingface.co/datasets/PKU-Alignment/PKU-SafeRLHF
    Paper: https://arxiv.org/pdf/2406.15513
    """

    def __init__(
        self,
        *,
        source: str = "PKU-Alignment/PKU-SafeRLHF",
        include_safe_prompts: bool = True,
        filter_harm_categories: Optional[
            list[
                Literal[
                    "Animal Abuse",
                    "Copyright Issues",
                    "Cybercrime",
                    "Discriminatory Behavior",
                    "Disrupting Public Order",
                    "Drugs",
                    "Economic Crime",
                    "Endangering National Security",
                    "Endangering Public Health",
                    "Environmental Damage",
                    "Human Trafficking",
                    "Insulting Behavior",
                    "Mental Manipulation",
                    "Physical Harm",
                    "Privacy Violation",
                    "Psychological Harm",
                    "Sexual Content",
                    "Violence",
                    "White-Collar Crime",
                ]
            ]
        ] = None,
    ):
        """
        Initialize the PKU-SafeRLHF dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "PKU-Alignment/PKU-SafeRLHF".
            include_safe_prompts: All prompts returned if True; only unsafe subset if False.
            filter_harm_categories: List of harm categories to filter. Defaults to None (all categories).
                Only prompts with at least one matching category are included.
        """
        self.source = source
        self.include_safe_prompts = include_safe_prompts
        self.filter_harm_categories = filter_harm_categories

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "pku_safe_rlhf"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch PKU-SafeRLHF dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the PKU-SafeRLHF prompts.
        """
        logger.info(f"Loading PKU-SafeRLHF dataset from {self.source}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.source,
            config="default",
            cache=cache,
            split="train",
        )

        seed_prompts = []

        for item in data:
            is_unsafe = not (item["is_response_0_safe"] and item["is_response_1_safe"])

            # Skip if safe prompts are excluded and the item is safe
            if not self.include_safe_prompts and not is_unsafe:
                continue

            # Collect harm categories from both responses, avoid duplicates
            harm_categories = {
                category
                for response_key in ["response_0_harm_category", "response_1_harm_category"]
                for category, is_harmful in item[response_key].items()
                if is_harmful
            }

            # Add the prompt in two cases:
            # 1. No harm categories are provided for filtering
            # 2. At least one harm category matches the specified filter criteria
            if not self.filter_harm_categories or any(
                category in self.filter_harm_categories for category in harm_categories
            ):
                seed_prompts.append(
                    SeedPrompt(
                        value=item["prompt"],
                        data_type="text",
                        dataset_name=self.dataset_name,
                        harm_categories=sorted(harm_categories),
                        description=(
                            "This is a Hugging Face dataset that labels a prompt and 2 responses categorizing "
                            "their helpfulness or harmfulness. Only the 'prompt' column is extracted."
                        ),
                        source=f"https://huggingface.co/datasets/{self.source}",
                    )
                )

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from PKU-SafeRLHF dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
