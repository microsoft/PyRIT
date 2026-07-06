# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import cast

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class _JBBBehaviorsDataset(_RemoteDatasetLoader):
    """
    Loader for the JailbreakBench JBB-Behaviors dataset.

    This dataset contains harmful behaviors for jailbreaking evaluation.

    Reference: [@chao2024jailbreakbench]

    Content Warning: This dataset contains prompts aimed at provoking harmful responses
    and may contain offensive content. Users should check with their legal department
    before using these prompts against production LLMs.
    """

    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = cast(
        "dict[str, list[HarmCategory]]",
        {
            "disinformation": [HarmCategory.INFO_INTEGRITY],
            "economic harm": [HarmCategory.SCAMS],
            "expert advice": [HarmCategory.HEALTH_DIAGNOSIS, HarmCategory.LEGAL_ADVICE, HarmCategory.FINANCIAL_ADVICE],
            "fraud/deception": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
            "government decision-making": [HarmCategory.HIGH_RISK_GOVERNMENT],
            "harassment/discrimination": [
                HarmCategory.HARASSMENT,
                HarmCategory.HATESPEECH,
                HarmCategory.REPRESENTATIONAL,
            ],
            "malware/hacking": [HarmCategory.MALWARE],
            "physical harm": [HarmCategory.VIOLENT_CONTENT, HarmCategory.COORDINATION_HARM],
            "privacy": [HarmCategory.PPI],
            "sexual/adult content": [HarmCategory.SEXUAL_CONTENT],
        },
    )

    def __init__(
        self,
        *,
        source: str = "JailbreakBench/JBB-Behaviors",
        split: str = "behaviors",
    ) -> None:
        """
        Initialize the JBB-Behaviors dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "JailbreakBench/JBB-Behaviors".
            split: Dataset split to load. Defaults to "behaviors".
        """
        self.source = source
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "jbb_behaviors"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch JBB-Behaviors dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the JBB behaviors with harm_categories set.

        Raises:
            ValueError: If the dataset is empty after processing.
            Exception: If the dataset cannot be loaded or processed.
        """
        try:
            logger.info(f"Loading JBB-Behaviors dataset from {self.source}")

            # Load from HuggingFace
            # Note: JBB-Behaviors has 'harmful' and 'benign' splits
            data = await self._fetch_from_huggingface(
                dataset_name=self.source,
                config=self.split,
                split="harmful",
                cache=cache,
            )

            # Define common metadata
            common_metadata = {
                "dataset_name": self.dataset_name,
                "authors": ["JailbreakBench Team"],
                "description": (
                    "A dataset of harmful behaviors for jailbreaking evaluation from JailbreakBench. "
                    "Contains behaviors designed to test AI safety measures."
                ),
                "source": self.source,
                "data_type": "text",
                "name": "JBB-Behaviors",
            }

            seed_prompts = []

            for item in data:
                # Extract the required fields
                behavior = item.get("Behavior", "").strip()
                category = item.get("Category", "")

                if not behavior:
                    logger.warning("[JBB-Behaviors] Skipping item with empty behavior field")
                    continue

                standardized_categories = self._standardize_harm_categories(
                    category,
                    alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
                )

                # Create SeedPrompt object with all metadata
                seed_prompt = SeedPrompt(
                    value=behavior,
                    harm_categories=standardized_categories,
                    groups=[category] if category else [],
                    metadata={
                        "jbb_category": category,
                        "original_source": "JailbreakBench",
                    },
                    **common_metadata,  # type: ignore[ty:invalid-argument-type]
                )

                seed_prompts.append(seed_prompt)

            if not seed_prompts:
                raise ValueError("SeedDataset cannot be empty.")

            logger.info(f"Successfully loaded {len(seed_prompts)} behaviors from JBB-Behaviors dataset")

            return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

        except Exception as e:
            logger.error(f"Failed to load JBB-Behaviors dataset: {str(e)}")
            raise Exception(f"Error loading JBB-Behaviors dataset: {str(e)}") from e
